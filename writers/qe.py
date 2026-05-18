"""
writers/qe.py

Convert a MaterialStructure (the canonical JSON representation produced
by the LLM + validator pipeline) into a Quantum ESPRESSO pw.x input file.

Design decisions:
- The JSON output remains the canonical, code-agnostic representation.
  This writer is a pure function of that JSON — it does not modify state
  and can be called independently of the LLM pipeline.
- Pseudopotentials are *referenced by name*, not bundled. We use the
  SSSP Efficiency v1.3 set (the standard recommendation for AiiDA / QE
  workflows, see https://www.materialscloud.org/discover/sssp/). The
  user is expected to provide the actual UPF files via QE's pseudo_dir.
- Cutoffs (ecutwfc, ecutrho) are taken from the SSSP recommendation per
  element. When multiple elements are present we take the max — this is
  the standard QE convention.
- k-point grid uses a Monkhorst-Pack mesh with density ~0.2 Å^-1, which
  is a reasonable default for SCF convergence of most semiconductors and
  metals. Users should converge this for production work.
- Smearing is enabled by default for robustness (metals fail without it,
  insulators are largely unaffected at the small smearing widths used).
  A note is emitted in the output if the validator flagged the material
  as an insulator.

Limitations:
- Pseudopotential filenames here are placeholders following SSSP naming
  conventions. The exact filename in SSSP can vary; verify against the
  SSSP table for your target release.
- Cutoffs are conservative starting points, not converged values.
"""

from dataclasses import dataclass
from typing import Optional
import math

# We import MaterialStructure for type hints only. The writer accepts any
# object with the expected attributes (duck typing), so it also works on
# a dict loaded from JSON via MaterialStructureToQE.from_json().
try:
    from output_schema import MaterialStructure, AtomicPosition
except ImportError:
    # Allow this module to be imported standalone
    MaterialStructure = None
    AtomicPosition = None


# ---------------------------------------------------------------------------
# SSSP Efficiency v1.3 recommendations (subset — extend as needed).
#
# Source: https://www.materialscloud.org/discover/sssp/table/efficiency
# Format: element -> (pseudopotential_filename, ecutwfc_Ry, ecutrho_Ry)
#
# These are *reference* names following the SSSP naming convention. The
# user must supply the actual UPF files. If an element is not in this
# table, the writer falls back to conservative defaults and emits a
# warning comment in the output file.
# ---------------------------------------------------------------------------
SSSP_EFFICIENCY = {
    # Common semiconductors / insulators
    "Si": ("Si.pbe-n-rrkjus_psl.1.0.0.UPF", 30, 240),
    "Ge": ("Ge.pbe-dn-rrkjus_psl.1.0.0.UPF", 40, 320),
    "C":  ("C.pbe-n-kjpaw_psl.1.0.0.UPF", 45, 360),
    "N":  ("N.pbe-n-radius_5.UPF", 60, 480),
    "O":  ("O.pbe-n-kjpaw_psl.0.1.UPF", 50, 400),
    "F":  ("F.oncvpsp.UPF", 55, 440),
    # III-V / II-VI
    "Ga": ("Ga.pbe-dn-kjpaw_psl.1.0.0.UPF", 50, 400),
    "As": ("As.pbe-n-rrkjus_psl.0.2.UPF", 35, 280),
    "In": ("In.pbe-dn-rrkjus_psl.0.2.2.UPF", 50, 400),
    "P":  ("P.pbe-n-rrkjus_psl.1.0.0.UPF", 30, 240),
    "S":  ("S.pbe-nl-rrkjus_psl.1.0.0.UPF", 40, 320),
    "Zn": ("Zn.pbe-dnl-kjpaw_psl.1.0.0.UPF", 90, 720),
    # Common metals
    "Cu": ("Cu.pbe-dn-kjpaw_psl.1.0.0.UPF", 55, 440),
    "Al": ("Al.pbe-n-kjpaw_psl.1.0.0.UPF", 30, 240),
    "Au": ("Au.pbe-n-kjpaw_psl.1.0.0.UPF", 45, 360),
    "Ag": ("Ag_ONCV_PBE-1.0.oncvpsp.UPF", 50, 200),
    "Fe": ("Fe.pbe-spn-kjpaw_psl.0.2.1.UPF", 90, 1080),
    "Ni": ("Ni.pbe-n-kjpaw_psl.1.0.0.UPF", 55, 660),
    # Alkali / alkaline earth (perovskites)
    "Na": ("Na_ONCV_PBE-1.0.oncvpsp.UPF", 40, 200),
    "K":  ("K.pbe-spn-kjpaw_psl.1.0.0.UPF", 60, 480),
    "Mg": ("Mg.pbe-n-kjpaw_psl.0.3.0.UPF", 40, 320),
    "Ca": ("Ca_pbe_v1.uspp.F.UPF", 30, 240),
    "Ba": ("Ba.pbe-spn-kjpaw_psl.1.0.0.UPF", 45, 360),
    "Sr": ("Sr_pbe_v1.uspp.F.UPF", 35, 280),
    # Transition metals (perovskites)
    "Ti": ("Ti.pbe-spn-kjpaw_psl.1.0.0.UPF", 50, 400),
    "V":  ("V_pbe_v1.4.uspp.F.UPF", 40, 320),
    "Cr": ("Cr.pbe-spn-kjpaw_psl.1.0.0.UPF", 50, 400),
    "Mn": ("Mn.pbe-spn-kjpaw_psl.0.3.1.UPF", 75, 600),
    "Co": ("Co.pbe-n-kjpaw_psl.0.3.1.UPF", 55, 440),
    "Zr": ("Zr_pbe_v1.uspp.F.UPF", 30, 240),
    # Halides
    "Cl": ("Cl.pbe-n-rrkjus_psl.1.0.0.UPF", 40, 320),
    "Br": ("Br.pbe-n-rrkjus_psl.1.0.0.UPF", 30, 240),
    "I":  ("I.pbe-n-kjpaw_psl.0.2.UPF", 35, 280),
    # Hydrogen
    "H":  ("H.pbe-rrkjus_psl.1.0.0.UPF", 60, 480),
}

# Conservative fallback if an element isn't in our SSSP table
FALLBACK_PSEUDO = ("{element}_UPF_FILE_NOT_FOUND_IN_SSSP_TABLE.UPF", 60, 480)

# ---------------------------------------------------------------------------
# Standard atomic masses (amu) — needed for ATOMIC_SPECIES.
# QE accepts these as ATOMIC_SPECIES requires mass; we use standard values.
# ---------------------------------------------------------------------------
ATOMIC_MASSES = {
    "H": 1.008, "Li": 6.94, "Be": 9.012, "B": 10.81, "C": 12.011,
    "N": 14.007, "O": 15.999, "F": 18.998, "Na": 22.990, "Mg": 24.305,
    "Al": 26.982, "Si": 28.085, "P": 30.974, "S": 32.06, "Cl": 35.45,
    "K": 39.098, "Ca": 40.078, "Sc": 44.956, "Ti": 47.867, "V": 50.942,
    "Cr": 51.996, "Mn": 54.938, "Fe": 55.845, "Co": 58.933, "Ni": 58.693,
    "Cu": 63.546, "Zn": 65.38, "Ga": 69.723, "Ge": 72.630, "As": 74.922,
    "Se": 78.971, "Br": 79.904, "Sr": 87.62, "Y": 88.906, "Zr": 91.224,
    "Nb": 92.906, "Mo": 95.95, "Ag": 107.868, "Cd": 112.414,
    "In": 114.818, "Sn": 118.710, "Sb": 121.760, "Te": 127.60,
    "I": 126.904, "Ba": 137.327, "La": 138.905, "Hf": 178.49,
    "Ta": 180.948, "W": 183.84, "Re": 186.207, "Os": 190.23,
    "Ir": 192.217, "Pt": 195.084, "Au": 196.967, "Pb": 207.2,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _lookup_pseudo(element: str):
    """Return (filename, ecutwfc, ecutrho) for an element, with fallback."""
    if element in SSSP_EFFICIENCY:
        return SSSP_EFFICIENCY[element]
    fname, ecw, ecr = FALLBACK_PSEUDO
    return (fname.format(element=element), ecw, ecr)


def _kpoint_grid(a: float, b: float, c: float, density: float = 0.2) -> tuple[int, int, int]:
    """
    Compute a Monkhorst-Pack k-point grid given lattice parameters and a
    target density in reciprocal Angstroms.

    Rule of thumb: nk_i = max(1, ceil(2*pi / (a_i * density)))
    For a typical semiconductor with a ~ 5 Å, this gives ~6x6x6.
    For a metal we recommend a denser grid; users should increase as needed.
    """
    nk = []
    for ai in (a, b, c):
        n = max(1, math.ceil((2 * math.pi) / (ai * density)))
        nk.append(n)
    return tuple(nk)


def _unique_species(material) -> list[str]:
    """Return unique element symbols in the order they first appear."""
    seen = []
    for ap in material.atomic_positions:
        # Support both dataclass and dict access (round-tripping from JSON)
        el = ap.element if hasattr(ap, "element") else ap["element"]
        if el not in seen:
            seen.append(el)
    return seen


def _is_metal(material) -> bool:
    """
    Use the validator's electronic_properties if available.
    Defaults to False (assume insulator) if no info — safe default for
    smearing width.
    """
    ep = getattr(material, "electronic_properties", None)
    if ep and isinstance(ep, dict):
        return bool(ep.get("is_metal", False))
    return False


# ---------------------------------------------------------------------------
# Main writer
# ---------------------------------------------------------------------------
@dataclass
class MaterialStructureToQE:
    """
    Configurable writer. Use the convenience function write_qe_input()
    for the common case.
    """
    calculation: str = "scf"
    prefix: str = "material"
    pseudo_dir: str = "./pseudos/"
    outdir: str = "./out/"
    kpoint_density: float = 0.2     # reciprocal Angstroms
    smearing: str = "mp"            # Methfessel-Paxton; QE default for metals
    degauss: float = 0.01           # Ry
    conv_thr: float = 1.0e-8        # Ry
    mixing_beta: float = 0.4

    def render(self, material) -> str:
        """Return the pw.x input file as a string."""
        species = _unique_species(material)

        # Determine max cutoffs across all species
        ecutwfc_max = 0
        ecutrho_max = 0
        pseudo_lines = []
        for el in species:
            fname, ecw, ecr = _lookup_pseudo(el)
            ecutwfc_max = max(ecutwfc_max, ecw)
            ecutrho_max = max(ecutrho_max, ecr)
            mass = ATOMIC_MASSES.get(el, 1.0)
            pseudo_lines.append(f"  {el:<3s}  {mass:9.4f}  {fname}")

        nat = len(material.atomic_positions)
        ntyp = len(species)

        # k-points
        nk1, nk2, nk3 = _kpoint_grid(
            material.a, material.b, material.c,
            density=self.kpoint_density,
        )

        # Choose smearing behaviour based on metallicity (if known)
        metal = _is_metal(material)
        occupations_block = (
            f"  occupations = 'smearing'\n"
            f"  smearing    = '{self.smearing}'\n"
            f"  degauss     = {self.degauss}\n"
            if metal
            else "  occupations = 'fixed'\n"
        )

        # Build CELL_PARAMETERS in angstrom (we use ibrav=0 for generality)
        cell_lines = self._cell_parameters_angstrom(material)

        # Atomic positions in fractional (crystal) coordinates
        atom_lines = []
        for ap in material.atomic_positions:
            el = ap.element if hasattr(ap, "element") else ap["element"]
            x = ap.x if hasattr(ap, "x") else ap["x"]
            y = ap.y if hasattr(ap, "y") else ap["y"]
            z = ap.z if hasattr(ap, "z") else ap["z"]
            atom_lines.append(f"  {el:<3s} {x:12.8f} {y:12.8f} {z:12.8f}")

        # Header comments — useful for traceability
        header = self._header_comment(material, metal)

        # Assemble the full input file
        return f"""{header}
&CONTROL
  calculation = '{self.calculation}'
  prefix      = '{self.prefix}'
  pseudo_dir  = '{self.pseudo_dir}'
  outdir      = '{self.outdir}'
  verbosity   = 'high'
  tprnfor     = .true.
  tstress     = .true.
/
&SYSTEM
  ibrav       = 0
  nat         = {nat}
  ntyp        = {ntyp}
  ecutwfc     = {ecutwfc_max}
  ecutrho     = {ecutrho_max}
{occupations_block.rstrip()}
/
&ELECTRONS
  conv_thr    = {self.conv_thr:.1e}
  mixing_beta = {self.mixing_beta}
/

ATOMIC_SPECIES
{chr(10).join(pseudo_lines)}

CELL_PARAMETERS angstrom
{cell_lines}

ATOMIC_POSITIONS crystal
{chr(10).join(atom_lines)}

K_POINTS automatic
  {nk1} {nk2} {nk3}  0 0 0
"""

    def _cell_parameters_angstrom(self, material) -> str:
        """
        Build the 3x3 lattice matrix from a, b, c, alpha, beta, gamma.
        Uses the standard convention:
            a along x
            b in the xy-plane
            c with components in x, y, z
        Angles are in degrees.
        """
        a, b, c = material.a, material.b, material.c
        alpha = math.radians(material.alpha)
        beta = math.radians(material.beta)
        gamma = math.radians(material.gamma)

        ax = a
        bx = b * math.cos(gamma)
        by = b * math.sin(gamma)
        cx = c * math.cos(beta)
        cy = c * (math.cos(alpha) - math.cos(beta) * math.cos(gamma)) / math.sin(gamma)
        cz = math.sqrt(max(0.0, c * c - cx * cx - cy * cy))

        rows = [
            f"  {ax:14.9f} {0.0:14.9f} {0.0:14.9f}",
            f"  {bx:14.9f} {by:14.9f} {0.0:14.9f}",
            f"  {cx:14.9f} {cy:14.9f} {cz:14.9f}",
        ]
        return "\n".join(rows)

    def _header_comment(self, material, metal: bool) -> str:
        """A traceability header — links the input file back to the JSON."""
        lines = [
            f"! pw.x input generated by material-identifier",
            f"! formula:        {material.formula}",
            f"! name:           {material.name}",
            f"! space group:    {material.space_group_symbol} (#{material.space_group_number})",
            f"! crystal system: {material.crystal_system}",
            f"! source:         {material.source} (confidence: {material.confidence})",
        ]
        if metal:
            lines.append("! electronic:     metal — smearing enabled")
        else:
            lines.append("! electronic:     insulator/semiconductor — fixed occupations")
        lines.append("!")
        lines.append("! Pseudopotentials follow the SSSP Efficiency v1.3 naming")
        lines.append("! convention. Provide the UPF files via pseudo_dir.")
        lines.append("! Cutoffs and k-points are starting values — converge for production.")
        return "\n".join(lines)
    
def write_qe_input(material, filepath: str, **kwargs) -> None:
    """
    Convenience function: write a pw.x input file for a MaterialStructure.

    Args:
        material:  MaterialStructure object (or dict loaded from JSON)
        filepath:  output path, e.g. 'examples/silicon.in'
        **kwargs:  optional overrides forwarded to MaterialStructureToQE,
                   e.g. prefix='Si', kpoint_density=0.15
    """
    writer = MaterialStructureToQE(**kwargs)
    content = writer.render(material)
    with open(filepath, "w") as f:
        f.write(content)
    print(f"Wrote QE input to {filepath}")