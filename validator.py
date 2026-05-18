"""
validator.py

Cross-validates LLM-inferred material properties against the Materials Project
database. 

Two types of validation:
    1. Structural — compares lattice parameters a, b, c against experimental data
    2. Electronic — retrieves band gap, direct/indirect nature, and metallicity
                    relevant for GW and BSE calculations
"""

from mp_api.client import MPRester
import os

TOLERANCE = 0.5  # Angstroms — max acceptable difference in lattice parameters a, b, c
                 # LLM infers from mixed experimental/computational literature
                 # Flags conventional vs primitive cell differences for user review

def validate_with_mp(material):
    """
    Query the Materials Project for the material formula and return
    a validation report comparing Gemini output against database values.
    
    Args:
        material: MaterialStructure object from output_schema.py
    
    Returns:
        dict with structural validation and electronic properties
    """
    api_key = os.getenv("MP_API_KEY")

    with MPRester(api_key) as mpr:
        results = mpr.materials.summary.search(
            formula=material.formula,
            fields=[
                "material_id",
                "formula_pretty",
                "symmetry",
                "structure",
                "energy_above_hull",   # distance from convex hull — 0 means most stable phase
                "is_stable",           # True if material is thermodynamically stable
                # Electronic properties for GW/BSE
                "band_gap",
                "is_gap_direct",
                "is_metal",
            ]
        )

        if not results:
            return {
                "status": "not_found",
                "message": f"{material.formula} not found in Materials Project",
                "parameter_comparison": None,
                "electronic_properties": None
            }

        # Select most stable entry
        # energy_above_hull = 0 means it is on the convex hull (most stable)
        stable = [r for r in results if r.is_stable]
        best = stable[0] if stable else sorted(
            results, key=lambda x: x.energy_above_hull
        )[0]

        mp_lattice = best.structure.lattice

        # --- Structural validation ---
        comparison = {
            "a": {"gemini": material.a,
                   "mp": round(mp_lattice.a, 3),
                  "diff": round(abs(material.a - mp_lattice.a), 3)},
            "b": {"gemini": material.b,
                  "mp": round(mp_lattice.b, 3),
                  "diff": round(abs(material.b - mp_lattice.b), 3)},
            "c": {"gemini": material.c, 
                  "mp": round(mp_lattice.c, 3),
                  "diff": round(abs(material.c - mp_lattice.c), 3)},
        }
        
        #Flag parameters a, b, c exceeding tolerance and set status accordingly
        flags = [k for k, v in comparison.items() if v["diff"] > TOLERANCE]
        status = "validated" if not flags else "mismatch"

        # --- Electronic properties for GW/BSE ---
        # band_gap: DFT underestimates it — GW corrects this
        # is_gap_direct: direct gap materials show stronger excitonic effects in BSE
        # is_metal: BSE is not meaningful for metals
        electronic_properties = {
            "band_gap_ev": best.band_gap,
            "is_gap_direct": best.is_gap_direct,
            "is_metal": best.is_metal,
            "gw_recommended": not best.is_metal,
            "bse_recommended": not best.is_metal and best.band_gap > 0,
            "note": (
                "Metal — GW/BSE not recommended" if best.is_metal
                else "Semiconductor/insulator — GW/BSE applicable"
            )
        }


        return {
            "status": status,
            "mp_id": best.material_id,
            "mp_formula": best.formula_pretty,
            "mp_space_group": best.symmetry.symbol,
            "parameter_comparison": comparison,
            "flagged_parameters": flags,
            "message": (
                "All parameters within tolerance" if not flags
                else f"Large difference in: {', '.join(flags)}"
            ),
            "electronic_properties": electronic_properties
        }