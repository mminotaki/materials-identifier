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

def identify_material(user_description: str, output_path: str = None):
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

    if output_path:
        material.save(output_path)

    return material, validation


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

    args = parser.parse_args()

    if args.description:
        # User provided a custom description
        output_path = args.output or f"examples/{args.description[:20].replace(' ', '_')}.json"
        identify_material(args.description, output_path=output_path)

    elif args.run_examples:
        # Run all built-in examples
        examples = [
            ("silicon in the diamond cubic structure",      "examples/silicon_diamond.json"),
            ("face-centred cubic copper",                   "examples/copper_fcc.json"),
            ("a perovskite oxide with titanium and barium", "examples/barium_titanate.json"),
            ("wurtzite gallium nitride",                    "examples/gallium_nitride_wurtzite.json"),
            ("iron in the body-centred cubic structure",    "examples/iron_bcc.json"),
        ]
        for description, path in examples:
            identify_material(description, output_path=path)
            print("-" * 50)
            time.sleep(15)

    else:
        # Interactive mode
        print("Material Identifier — enter a material description or use --help")
        description = input("Description: ")
        output_path = f"examples/{description[:20].replace(' ', '_')}.json"
        identify_material(description, output_path=output_path)

