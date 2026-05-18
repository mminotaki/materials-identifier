"""
expansion.py

Generate expanded sets of MaterialStructure objects from a single
input structure. Used to populate seed datasets for MLIP training.

Three strain modes are supported:
    - Isotropic: uniform scaling of a, b, c. Samples volumetric response.
    - Uniaxial: independent scaling of a, b, or c individually.
                Samples direction-dependent elastic response.
    - EOS-style: dense symmetric isotropic strain grid for fitting
                 Birch-Murnaghan or Vinet equations of state.

Design decisions:
- Pure functions: input MaterialStructure is never mutated; new
  copies are returned. Easy to compose, easy to test.
- Fractional atomic coordinates are preserved unchanged under strain,
  which is the whole point of fractional coordinates — they describe
  relative positions inside the cell.
- Each variant's `notes` field is overwritten with a human-readable
  description of the transformation, e.g. "isotropic strain -2.0%".
  This is also used downstream to construct output filenames.
- Strain values are passed as fractional deformations (epsilon),
  i.e. 0.02 means +2%, -0.05 means -5%.
"""

from copy import deepcopy
from output_schema import MaterialStructure, AtomicPosition


def _apply_lattice_scaling(material: MaterialStructure,
                           sa: float, sb: float, sc: float,
                           label: str) -> MaterialStructure:
    """
    Internal helper. Returns a new MaterialStructure with lattice
    parameters a, b, c multiplied by (1 + sa), (1 + sb), (1 + sc).
    Angles and fractional atomic positions are unchanged.

    The `label` is written into the `notes` field for traceability.
    """
    new = deepcopy(material)
    new.a = material.a * (1.0 + sa)
    new.b = material.b * (1.0 + sb)
    new.c = material.c * (1.0 + sc)
    new.notes = label
    # Clear validation/electronic properties — they were computed
    # for the *original* lattice and no longer apply.
    new.validation = None
    return new


def generate_isotropic_strain(material: MaterialStructure,
                              strains=(-0.02, -0.01, 0.01, 0.02)
                              ) -> list[MaterialStructure]:
    """
    Apply uniform strain to all three lattice vectors simultaneously.

    Used to sample volumetric response (bulk modulus, P-V curves).
    """
    variants = []
    for eps in strains:
        label = f"isotropic strain {eps:+.2%}"
        variants.append(_apply_lattice_scaling(material, eps, eps, eps, label))
    return variants


def generate_uniaxial_strain(material: MaterialStructure,
                             strains=(-0.02, 0.02),
                             axes=("a", "b", "c")
                             ) -> list[MaterialStructure]:
    """
    Apply strain along one crystallographic axis at a time.

    Note: this strains the a, b, c lattice *parameters* independently.
    For cubic crystals this is unambiguous. For lower-symmetry crystals
    (hexagonal, tetragonal, etc.) "uniaxial along a" means scaling the
    a-vector only — physically meaningful but not identical to Cartesian
    x/y/z uniaxial strain.
    """
    variants = []
    for axis in axes:
        for eps in strains:
            sa = eps if axis == "a" else 0.0
            sb = eps if axis == "b" else 0.0
            sc = eps if axis == "c" else 0.0
            label = f"uniaxial strain {axis}={eps:+.2%}"
            variants.append(_apply_lattice_scaling(material, sa, sb, sc, label))
    return variants


def generate_eos_strain(material: MaterialStructure,
                        eps_range: float = 0.05,
                        n_points: int = 11
                        ) -> list[MaterialStructure]:
    """
    Generate a dense, symmetric grid of isotropic strains suitable
    for fitting an equation of state (Birch-Murnaghan, Vinet, etc).

    Default: 11 points from -5% to +5% in 1% steps, including 0%
    (the equilibrium structure) for completeness.
    """
    if n_points < 2:
        raise ValueError("n_points must be >= 2")
    step = (2.0 * eps_range) / (n_points - 1)
    strains = [-eps_range + i * step for i in range(n_points)]

    variants = []
    for eps in strains:
        label = f"EOS strain {eps:+.2%}"
        variants.append(_apply_lattice_scaling(material, eps, eps, eps, label))
    return variants


def generate_strained(material: MaterialStructure,
                      modes=("isotropic", "uniaxial", "eos")
                      ) -> list[MaterialStructure]:
    """
    Convenience wrapper: produce all requested strain types with
    sensible default parameters.

    Returns a single flat list of MaterialStructure objects across
    all requested modes.
    """
    all_variants = []
    if "isotropic" in modes:
        all_variants.extend(generate_isotropic_strain(material))
    if "uniaxial" in modes:
        all_variants.extend(generate_uniaxial_strain(material))
    if "eos" in modes:
        all_variants.extend(generate_eos_strain(material))
    return all_variants


# ---------------------------------------------------------------------------
# Rattled configurations
# ---------------------------------------------------------------------------

import math
import random


def _lattice_matrix(material: MaterialStructure):
    """
    Build the 3x3 lattice matrix from a, b, c, alpha, beta, gamma.
    Same convention as the QE writer: a along x, b in the xy-plane,
    c with components in x, y, z.
    Returns a tuple of three row vectors.
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

    return ((ax, 0.0, 0.0),
            (bx, by, 0.0),
            (cx, cy, cz))


def _frac_to_cart(frac, lattice):
    """fractional (x,y,z) -> Cartesian via M^T @ frac."""
    x, y, z = frac
    cx = x * lattice[0][0] + y * lattice[1][0] + z * lattice[2][0]
    cy = x * lattice[0][1] + y * lattice[1][1] + z * lattice[2][1]
    cz = x * lattice[0][2] + y * lattice[1][2] + z * lattice[2][2]
    return (cx, cy, cz)


def _cart_to_frac(cart, lattice):
    """
    Cartesian (x,y,z) -> fractional. Solves M^T @ frac = cart by inverting M^T.
    Implemented inline to avoid a numpy dependency.
    """
    # Extract M^T (column vectors are a, b, c lattice vectors)
    a1, a2, a3 = lattice[0]
    b1, b2, b3 = lattice[1]
    c1, c2, c3 = lattice[2]

    # Standard 3x3 inverse via cofactors
    det = (a1 * (b2 * c3 - b3 * c2)
           - a2 * (b1 * c3 - b3 * c1)
           + a3 * (b1 * c2 - b2 * c1))
    inv00 = (b2 * c3 - b3 * c2) / det
    inv01 = (a3 * c2 - a2 * c3) / det
    inv02 = (a2 * b3 - a3 * b2) / det
    inv10 = (b3 * c1 - b1 * c3) / det
    inv11 = (a1 * c3 - a3 * c1) / det
    inv12 = (a3 * b1 - a1 * b3) / det
    inv20 = (b1 * c2 - b2 * c1) / det
    inv21 = (a2 * c1 - a1 * c2) / det
    inv22 = (a1 * b2 - a2 * b1) / det

    x, y, z = cart
    fx = inv00 * x + inv01 * y + inv02 * z
    fy = inv10 * x + inv11 * y + inv12 * z
    fz = inv20 * x + inv21 * y + inv22 * z
    return (fx, fy, fz)


def generate_rattled(material: MaterialStructure,
                     amplitude: float = 0.05,
                     n: int = 5,
                     seed: int = None
                     ) -> list[MaterialStructure]:
    """
    Generate N copies of the structure with random Cartesian displacements
    added to each atom. Used to sample the energy landscape near equilibrium
    for MLIP training.

    Args:
        material:  reference MaterialStructure (lattice + positions)
        amplitude: maximum displacement per atom per axis, in Angstroms.
                   0.05 Å is a sensible default for near-equilibrium sampling.
        n:         number of rattled variants to generate
        seed:      RNG seed for reproducibility. If None, results differ
                   each run.

    Notes:
        Displacements are sampled uniformly from [-amplitude, +amplitude]
        in each Cartesian direction. Lattice parameters are unchanged.
    """
    rng = random.Random(seed)
    lattice = _lattice_matrix(material)

    variants = []
    for i in range(n):
        new = deepcopy(material)
        new.notes = f"rattled amplitude={amplitude} A, seed={seed}, index={i+1}"
        new.validation = None

        for j, atom in enumerate(new.atomic_positions):
            # Convert this atom's fractional position to Cartesian
            cart = _frac_to_cart((atom.x, atom.y, atom.z), lattice)
            # Add a random displacement in each Cartesian direction
            dx = rng.uniform(-amplitude, amplitude)
            dy = rng.uniform(-amplitude, amplitude)
            dz = rng.uniform(-amplitude, amplitude)
            cart_new = (cart[0] + dx, cart[1] + dy, cart[2] + dz)
            # Convert back to fractional and store
            fx, fy, fz = _cart_to_frac(cart_new, lattice)
            atom.x, atom.y, atom.z = fx, fy, fz

        variants.append(new)
    return variants

# ---------------------------------------------------------------------------
# Supercells
# ---------------------------------------------------------------------------

def make_supercell(material: MaterialStructure,
                   scaling: tuple = (2, 2, 2)
                   ) -> MaterialStructure:
    """
    Expand the unit cell into a supercell.

    Args:
        material: input MaterialStructure
        scaling:  (na, nb, nc) integer multipliers for each lattice vector

    Returns:
        A single new MaterialStructure with:
          - lattice vectors scaled by (na, nb, nc)
          - atomic basis replicated na*nb*nc times
          - fractional coordinates rescaled to the new (larger) cell

    Notes:
        Angles are preserved. Only diagonal supercell transformations are
        supported here — for non-diagonal (e.g. for surfaces or rotated
        slabs) a more general 3x3 transformation matrix would be needed.
    """
    na, nb, nc = scaling
    if not all(isinstance(n, int) and n >= 1 for n in (na, nb, nc)):
        raise ValueError("scaling must be a tuple of positive integers")

    new = deepcopy(material)
    new.a = material.a * na
    new.b = material.b * nb
    new.c = material.c * nc
    new.notes = f"supercell {na}x{nb}x{nc}"
    new.validation = None

    # Replicate each atom across the supercell
    new_positions = []
    for i in range(na):
        for j in range(nb):
            for k in range(nc):
                for atom in material.atomic_positions:
                    new_positions.append(AtomicPosition(
                        element=atom.element,
                        x=(atom.x + i) / na,
                        y=(atom.y + j) / nb,
                        z=(atom.z + k) / nc,
                        wyckoff_position=atom.wyckoff_position,
                    ))
    new.atomic_positions = new_positions
    return new