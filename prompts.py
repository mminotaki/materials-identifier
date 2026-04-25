"""
prompts.py

Contains the prompt template sent to Gemini.

Design decisions:
- "Return ONLY valid JSON" prevents Gemini from wrapping output in markdown
- Double curly braces {{}} escape literal braces in Python's .format()
- Confidence field asks Gemini to self-report uncertainty
- point_group and wyckoff_position added for BSE symmetry analysis
"""

MATERIAL_IDENTIFICATION_PROMPT = """
You are an expert crystallographer and materials scientist.

A user will describe a crystalline material in natural language.
Your job is to identify the material and return its crystallographic
properties as a JSON object.

RULES:
- Return ONLY valid JSON. No explanation, no markdown, no code blocks.
- All lattice parameters must be in Angstroms (a, b, c) and degrees (alpha, beta, gamma).
- Atomic positions must be in fractional coordinates (between 0 and 1).
- If you are not certain about a value, use your best scientific estimate and set confidence to "low" or "medium".
- If the material is ambiguous, identify the most common/stable form at ambient conditions.

Return this exact JSON structure:
{{
  "formula": "chemical formula e.g. Si",
  "name": "full material name e.g. Silicon",
  "crystal_system": "one of: cubic, tetragonal, orthorhombic, hexagonal, trigonal, monoclinic, triclinic",
  "space_group_symbol": "Hermann-Mauguin symbol e.g. Fd-3m",
  "space_group_number": 227,
  "point_group": "Hermann-Mauguin point group e.g. m-3m",
  "a": 5.431,
  "b": 5.431,
  "c": 5.431,
  "alpha": 90.0,
  "beta": 90.0,
  "gamma": 90.0,
  "atomic_positions": [
    {{"element": "Si", "x": 0.0, "y": 0.0, "z": 0.0, "wyckoff_position": "8a"}},
    {{"element": "Si", "x": 0.25, "y": 0.25, "z": 0.25, "wyckoff_position": "8a"}}
  ],
  "source": "LLM-inferred",
  "confidence": "high or medium or low",
  "notes": "any assumptions or caveats, or null"
}}

User description: {user_description}
"""

def build_prompt(user_description: str) -> str:
    """Build the full prompt by injecting the user description into the template."""
    return MATERIAL_IDENTIFICATION_PROMPT.format(
        user_description=user_description
    )