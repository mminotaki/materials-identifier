"""
output_schema.py

Defines the structured output format for the Material Identifier framework.

The framework accepts natural-language material descriptions such as:
    - "silicon in the diamond cubic structure"
    - "a perovskite oxide with titanium and barium"
    - "face-centred cubic copper"
    - "wurtzite gallium nitride"

This schema captures the crystallographic information needed to generate
input files for DFT codes such as VASP, Quantum ESPRESSO, and CP2K.
It is intentionally code-agnostic — the same JSON output can be converted
to any DFT input format without modification.

Classes:
    AtomicPosition     — fractional coordinates of a single atom
    MaterialStructure  — full crystallographic description of a material
"""

# Two classes mirror the two levels of Gemini's JSON output:
#   MaterialStructure — the full unit cell (one per material)
#       └── AtomicPosition — one atom with fractional coordinates
# This maps directly to what DFT codes need: cell + basis.

from dataclasses import dataclass, asdict
from typing import Optional
import json

@dataclass
class AtomicPosition:
    element: str                          # e.g. "Si"
    x: float                              # fractional coordinate
    y: float                              # fractional coordinate
    z: float                              # fractional coordinate
    wyckoff_position: Optional[str] = None  # e.g. "4a" — symmetry site

@dataclass
class MaterialStructure:
    # --- Identity ---
    formula: str                          # e.g. "GaN"
    name: str                             # e.g. "Gallium Nitride"

    # --- Crystal symmetry ---
    crystal_system: str                   # e.g. "hexagonal"
    space_group_symbol: str               # e.g. "P6_3mc"
    space_group_number: int               # e.g. 186

    # --- Lattice parameters ---
    a: float                              # Angstroms
    b: float                              # Angstroms
    c: float                              # Angstroms
    alpha: float                          # degrees
    beta: float                           # degrees
    gamma: float                          # degrees

    # --- Atomic basis ---
    atomic_positions: list[AtomicPosition]

    # --- Metadata ---
    source: str                           # e.g. "LLM-inferred"
    confidence: str                       # "high" / "medium" / "low"

    # --- Optional fields (must come after required fields) ---
    point_group: Optional[str] = None     # e.g. "6mm" — needed for BSE symmetry analysis
    notes: Optional[str] = None           # any caveats or assumptions

    # --- Validation ---
    # Lattice parameter comparison against Materials Project.
    validation: Optional[dict] = None

    # --- Electronic Properties ---
    # Retrieved from Materials Project. Relevant for GW and BSE calculations:
    # - band_gap: needed to assess if GW correction is necessary
    # - is_gap_direct: determines if BSE will show bound excitons
    # - is_metal: BSE is not meaningful for metals
    electronic_properties: Optional[dict] = None

    def to_dict(self):                    # converts to dictionary
        return asdict(self)

    def to_json(self, indent=2):          # dictionary -> json string
        return json.dumps(self.to_dict(), indent=indent)

    def save(self, filepath: str):        # saves json -> examples folder
        with open(filepath, "w") as f:
            f.write(self.to_json())
        print(f"Saved to {filepath}")