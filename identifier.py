"""
identifier.py

Main pipeline for the Material Identifier framework.

Three responsibilities:
    1. Call Gemini with the user description -> call_gemini()
    2. Parse the JSON response into a MaterialStructure -> parse_response()
    3. Validate against Materials Project and save -> identify_material()
"""

import os
import json
import time
from dotenv import load_dotenv
from google import genai

from prompts import build_prompt
from output_schema import MaterialStructure, AtomicPosition
from validator import validate_with_mp
from writers import write_qe_input
from expansion import (
    generate_isotropic_strain,
    generate_uniaxial_strain,
    generate_eos_strain,
    generate_rattled,
    make_supercell,
)

load_dotenv()

def call_gemini(user_description: str) -> str:
    """Send the prompt to Gemini and return raw text response."""
    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    prompt = build_prompt(user_description)
    response = client.models.generate_content(
        model="gemini-2.5-flash", # current free tier model
        contents=prompt
    )
    return response.text


def parse_response(raw: str) -> MaterialStructure:
    """Parse Gemini's JSON response into a MaterialStructure object."""
    # Clean up response in case model adds markdown despite instructions
    clean = raw.strip()
    if clean.startswith("```"):
        clean = clean.split("```")[1]
        if clean.startswith("json"):
            clean = clean[4:]
    clean = clean.strip()

    data = json.loads(clean)

    # Build list of AtomicPosition objects from Gemini's atomic_positions array
    atomic_positions = [
        AtomicPosition(
            element=atom["element"],
            x=atom["x"],
            y=atom["y"],
            z=atom["z"],
            wyckoff_position=atom.get("wyckoff_position")  # optional field
        )
        for atom in data["atomic_positions"]
    ]

    return MaterialStructure(
        formula=data["formula"],
        name=data["name"],
        crystal_system=data["crystal_system"],
        space_group_symbol=data["space_group_symbol"],
        space_group_number=data["space_group_number"],
        a=data["a"],
        b=data["b"],
        c=data["c"],
        alpha=data["alpha"],
        beta=data["beta"],
        gamma=data["gamma"],
        atomic_positions=atomic_positions,
        source=data["source"],
        confidence=data["confidence"],
        notes=data.get("notes"),
        point_group=data.get("point_group")  # optional field
    )

def is_valid_material(user_description: str) -> bool:
    """Quick pre-check — ask Gemini if the description refers to a crystalline material."""
    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    for attempt in range(3):
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=f"""Does this description refer to a real crystalline material 
                that has a defined crystal structure? Answer only YES or NO.
                Description: {user_description}"""
            )
            answer = response.text.strip().upper()
            return answer.startswith("YES")
        except Exception as e:
            if attempt < 2:
                print(f"  Pre-check attempt {attempt + 1} failed, retrying in 40s...")
                time.sleep(40)
            else:
                # If pre-check fails assume valid and proceed
                print("  Pre-check unavailable — proceeding anyway")
                return True

def identify_material(user_description: str, output_path: str = None, formats: list[str] = None):
    """Full pipeline: description -> Gemini -> parsed structure -> validated -> saved."""
    print(f"\nIdentifying: '{user_description}'...")

    # Pre-check — validate input before calling the full pipeline
    if not is_valid_material(user_description):
        print(f"❌ '{user_description}' does not appear to be a crystalline material.")
        print("Please provide a description like 'silicon in the diamond cubic structure'.")
        return None, None

    # Retry up to 5 times with a delay to handle API rate limits and Gemini server overload
    for attempt in range(5):
        try:
            raw = call_gemini(user_description)
            break
        except Exception as e:
            if attempt < 4:
                print(f"  Attempt {attempt + 1} failed, retrying in 40s...")
                time.sleep(40)
            else:
                raise e

    material = parse_response(raw)

    print(f"Found: {material.name} ({material.formula})")
    print(f"Space group: {material.space_group_symbol} (#{material.space_group_number})")
    print(f"Confidence: {material.confidence}")

    # Validate against Materials Project
    print(f"Validating against Materials Project...")
    validation = validate_with_mp(material)
    print(f"Validation status: {validation['status']}")
    print(f"Message: {validation['message']}")
    if validation.get("parameter_comparison"):
        for param, values in validation["parameter_comparison"].items():
            flag = " ⚠️" if values["diff"] > 0.5 else " ✅"
            print(f"  {param}: Gemini={values['gemini']} MP={values['mp']} diff={values['diff']}{flag}")

    # Store validation and electronic properties back into material object
    # so they are included in the saved JSON file
    material.validation = validation
    material.electronic_properties = validation.get("electronic_properties")
    
    # Default to JSON only if no formats specified — preserves original behaviour
    if formats is None:
        formats = ["json"]

    if output_path:
        base, _ = os.path.splitext(output_path)

        if "json" in formats:
            json_path = base + ".json"
            material.save(json_path)

        if "qe" in formats:
            qe_path = base + ".in"
            write_qe_input(material, qe_path, prefix=material.formula)

    return material, validation

def _variant_filename(material, formula: str) -> str:
    """
    Build a clean, sortable filename suffix from a variant's notes field.
    Maps the human-readable note to a filesystem-safe slug.
    """
    notes = (material.notes or "").lower()

    # Strain variants
    if "isotropic strain" in notes:
        # "isotropic strain -2.00%" -> "strain_iso_-0.02"
        val = float(notes.split()[-1].rstrip("%")) / 100.0
        return f"{formula}_strain_iso_{val:+.2f}"
    if "uniaxial strain" in notes:
        # "uniaxial strain a=+2.00%" -> "strain_uni_a_+0.02"
        axis = notes.split("strain")[1].split("=")[0].strip()
        val = float(notes.split("=")[1].rstrip("%")) / 100.0
        return f"{formula}_strain_uni_{axis}_{val:+.2f}"
    if "eos strain" in notes:
        # "EOS strain +5.00%" -> "strain_eos_+0.05"
        val = float(notes.split()[-1].rstrip("%")) / 100.0
        return f"{formula}_strain_eos_{val:+.2f}"
    if "rattled" in notes:
        # "rattled amplitude=0.05 A, seed=42, index=3" -> "rattle_003"
        idx = int(notes.split("index=")[1])
        return f"{formula}_rattle_{idx:03d}"
    if "supercell" in notes:
        # "supercell 2x2x2" -> "supercell_2x2x2"
        size = notes.split()[-1]
        return f"{formula}_supercell_{size}"

    # Fallback — shouldn't happen, but better than crashing
    return f"{formula}_variant"


def expand_and_write(base_material, output_folder: str, expand_config: dict,
                     formats: list[str]) -> None:
    """
    Generate expanded variants from a base material and write them to disk
    as a folder of QE input files plus a manifest.

    Args:
        base_material:    MaterialStructure from identify_material
        output_folder:    path to a folder (created if missing)
        expand_config:    dict from CLI parsing with keys:
                          expansions, strain_modes, n_rattle,
                          rattle_amplitude, rattle_seed, supercell
        formats:          list of output formats. Note: variants are only
                          written as QE. JSON is reserved for the base.
    """
    os.makedirs(output_folder, exist_ok=True)
    formula = base_material.formula
    expansions = expand_config["expansions"]

    # --- Write base structure ---
    base_stem = os.path.join(output_folder, f"{formula}_base")
    if "json" in formats:
        base_material.save(base_stem + ".json")
    if "qe" in formats:
        write_qe_input(base_material, base_stem + ".in", prefix=formula)

    # --- Collect all variants ---
    variants = []

    if "strain" in expansions:
        strain_modes = expand_config["strain_modes"]
        if "iso" in strain_modes:
            variants.extend(generate_isotropic_strain(base_material))
        if "uni" in strain_modes:
            variants.extend(generate_uniaxial_strain(base_material))
        if "eos" in strain_modes:
            variants.extend(generate_eos_strain(base_material))

    if "rattle" in expansions:
        variants.extend(generate_rattled(
            base_material,
            amplitude=expand_config["rattle_amplitude"],
            n=expand_config["n_rattle"],
            seed=expand_config["rattle_seed"],
        ))

    if "supercell" in expansions:
        variants.append(make_supercell(
            base_material,
            scaling=expand_config["supercell"],
        ))

    # --- Write each variant as QE only ---
    manifest_entries = []
    for v in variants:
        stem = _variant_filename(v, formula)
        path = os.path.join(output_folder, stem + ".in")
        write_qe_input(v, path, prefix=formula)
        manifest_entries.append({
            "filename": stem + ".in",
            "transformation": v.notes,
            "lattice": {"a": v.a, "b": v.b, "c": v.c,
                        "alpha": v.alpha, "beta": v.beta, "gamma": v.gamma},
            "n_atoms": len(v.atomic_positions),
        })

    # --- Write manifest ---
    manifest = {
        "base_material": {
            "formula": base_material.formula,
            "name": base_material.name,
            "space_group_symbol": base_material.space_group_symbol,
            "space_group_number": base_material.space_group_number,
        },
        "expansions_applied": expansions,
        "expand_config": {
            "strain_modes": expand_config["strain_modes"] if "strain" in expansions else None,
            "n_rattle": expand_config["n_rattle"] if "rattle" in expansions else None,
            "rattle_amplitude": expand_config["rattle_amplitude"] if "rattle" in expansions else None,
            "rattle_seed": expand_config["rattle_seed"] if "rattle" in expansions else None,
            "supercell": list(expand_config["supercell"]) if "supercell" in expansions else None,
        },
        "n_variants": len(variants),
        "variants": manifest_entries,
    }
    manifest_path = os.path.join(output_folder, "manifest.json")
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"Wrote dataset to {output_folder}/")
    print(f"  Base structure + {len(variants)} variants + manifest.json")

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Identify a crystalline material from a natural language description."
    )
    parser.add_argument(
        "--description",
        type=str,
        help="Natural language material description e.g. 'silicon in the diamond cubic structure'",
        default=None
    )
    parser.add_argument(
        "--output",
        type=str,
        help="Output file path e.g. examples/silicon.json",
        default=None
    )
    parser.add_argument(
        "--run-examples",
        action="store_true",
        help="Run all 5 built-in example materials"
    )

    parser.add_argument(
        "--format",
        type=str,
        default="json",
        help="Comma-separated output formats: json, qe, or json,qe. Default: json"
    )


    parser.add_argument(
        "--expand",
        type=str,
        default=None,
        help=(
            "Generate expanded variants for MLIP training datasets. "
            "Comma-separated list of: strain, rattle, supercell. "
            "When set, --output is treated as a folder."
        )
    )
    parser.add_argument(
        "--strain",
        type=str,
        default="iso,uni,eos",
        help=(
            "Strain modes when --expand includes 'strain'. "
            "Comma-separated subset of: iso, uni, eos. Default: all three."
        )
    )
    parser.add_argument(
        "--n-rattle",
        type=int,
        default=5,
        help="Number of rattled variants when --expand includes 'rattle'. Default: 5"
    )
    parser.add_argument(
        "--rattle-amplitude",
        type=float,
        default=0.05,
        help="Cartesian rattle amplitude in Angstroms. Default: 0.05"
    )
    parser.add_argument(
        "--rattle-seed",
        type=int,
        default=42,
        help="RNG seed for rattle reproducibility. Default: 42"
    )
    parser.add_argument(
        "--supercell",
        type=str,
        default="2,2,2",
        help="Supercell scaling when --expand includes 'supercell'. Format: na,nb,nc. Default: 2,2,2"
    )

    args = parser.parse_args()

    args = parser.parse_args()

    # --- Format validation ---
    formats = [f.strip().lower() for f in args.format.split(",")]
    valid_formats = {"json", "qe"}
    invalid = set(formats) - valid_formats
    if invalid:
        parser.error(f"Unknown format(s): {invalid}. Choose from {valid_formats}.")

    # --- Expansion validation ---
    expansions = []
    if args.expand:
        expansions = [e.strip().lower() for e in args.expand.split(",")]
        valid_exp = {"strain", "rattle", "supercell"}
        invalid_exp = set(expansions) - valid_exp
        if invalid_exp:
            parser.error(f"Unknown expansion(s): {invalid_exp}. Choose from {valid_exp}.")

    # --- Strain mode validation ---
    strain_modes = [s.strip().lower() for s in args.strain.split(",")]
    valid_strain = {"iso", "uni", "eos"}
    invalid_strain = set(strain_modes) - valid_strain
    if invalid_strain:
        parser.error(f"Unknown strain mode(s): {invalid_strain}. Choose from {valid_strain}.")

    # --- Supercell parsing ---
    try:
        supercell_tuple = tuple(int(n) for n in args.supercell.split(","))
        if len(supercell_tuple) != 3 or any(n < 1 for n in supercell_tuple):
            raise ValueError
    except ValueError:
        parser.error(f"--supercell must be three positive integers like '2,2,2', got '{args.supercell}'")

    # Bundle expansion args into a dict for cleaner passing
    expand_config = {
        "expansions": expansions,
        "strain_modes": strain_modes,
        "n_rattle": args.n_rattle,
        "rattle_amplitude": args.rattle_amplitude,
        "rattle_seed": args.rattle_seed,
        "supercell": supercell_tuple,
    }
    
    if args.description:
        # User provided a custom description
        if expansions:
            # Expansion mode: --output is a folder
            output_folder = args.output or f"examples/{args.description[:20].replace(' ', '_')}_dataset"
            material, _ = identify_material(args.description, output_path=None, formats=formats)
            if material is not None:
                expand_and_write(material, output_folder, expand_config, formats)
        else:
            # Standard single-file mode (unchanged behaviour)
            output_path = args.output or f"examples/{args.description[:20].replace(' ', '_')}.json"
            identify_material(args.description, output_path=output_path, formats=formats)

    elif args.run_examples:
        # Run all built-in examples
        examples = [
            ("silicon in the diamond cubic structure",      "examples/silicon_diamond"),
            ("face-centred cubic copper",                   "examples/copper_fcc"),
            ("a perovskite oxide with titanium and barium", "examples/barium_titanate"),
            ("wurtzite gallium nitride",                    "examples/gallium_nitride_wurtzite"),
            ("iron in the body-centred cubic structure",    "examples/iron_bcc"),
        ]
        for description, base_path in examples:
            if expansions:
                # Folder per example
                material, _ = identify_material(description, output_path=None, formats=formats)
                if material is not None:
                    expand_and_write(material, base_path + "_dataset", expand_config, formats)
            else:
                # Standard single-file mode
                identify_material(description, output_path=base_path + ".json", formats=formats)
            print("-" * 50)
            time.sleep(15)

    else:
        # Interactive mode
        print("Material Identifier — enter a material description or use --help")
        description = input("Description: ")
        if expansions:
            output_folder = f"examples/{description[:20].replace(' ', '_')}_dataset"
            material, _ = identify_material(description, output_path=None, formats=formats)
            if material is not None:
                expand_and_write(material, output_folder, expand_config, formats)
        else:
            output_path = f"examples/{description[:20].replace(' ', '_')}.json"
            identify_material(description, output_path=output_path, formats=formats)