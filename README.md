# 🔬 Material Identifier

<p align="center">
  <img src="./media/banner.png" alt="Material Identifier" width="1000"/>
</p>

<p align="center">
  <a href="https://github.com/mminotaki/material-identifier" target="_blank" rel="noopener noreferrer">
    <img src="https://img.shields.io/badge/GitHub-material--identifier-181717?style=flat&logo=github&logoColor=white" alt="GitHub Repo" />
  </a>
  <a href="https://www.python.org" target="_blank" rel="noopener noreferrer">
    <img src="https://img.shields.io/badge/Python-3.12-blue?style=flat&logo=python&logoColor=white" alt="Python Version" />
  </a>
  <a href="https://aistudio.google.com" target="_blank" rel="noopener noreferrer">
    <img src="https://img.shields.io/badge/Gemini-2.5--Flash-orange?style=flat&logo=google&logoColor=white" alt="Gemini" />
  </a>
  <a href="https://materialsproject.org" target="_blank" rel="noopener noreferrer">
    <img src="https://img.shields.io/badge/Materials%20Project-validated-green?style=flat" alt="Materials Project" />
  </a>
  <a href="https://opensource.org/licenses/MIT" target="_blank" rel="noopener noreferrer">
    <img src="https://img.shields.io/badge/License-MIT-blue?style=flat" alt="License MIT" />
  </a>
</p>

---

## 📖 Overview

**Material Identifier** is a Python framework that uses Large Language Models (LLMs) to identify crystalline materials from natural-language descriptions and produce structured output files suitable as input for Density Functional Theory (DFT) calculations.

The framework accepts free-text descriptions such as:
- `"silicon in the diamond cubic structure"`
- `"a perovskite oxide with titanium and barium"`
- `"face-centred cubic copper"`
- `"wurtzite gallium nitride"`

And returns a structured JSON file containing the material's crystallographic properties.

---

## 🚀 Features

- 🧠 **LLM-driven identification** — natural language to crystallographic data via Gemini 2.5 Flash
- 🗂️ **DFT-code agnostic output** — structured JSON compatible with VASP, Quantum ESPRESSO, CP2K
- ✅ **Materials Project validation** — cross-checks lattice parameters against experimental data
- ⚡ **GW/BSE readiness** — retrieves band gap, direct/indirect nature, and metallicity
- 🔁 **Automatic retry logic** — handles API rate limits 
- 🖥️ **Interactive CLI** — accepts custom descriptions at runtime

---

## 📂 Project Structure

```
material-identifier/
├── identifier.py        ← main pipeline (call LLM, parse, validate, save)
├── prompts.py           ← prompt templates for Gemini
├── output_schema.py     ← MaterialStructure dataclass definition
├── validator.py         ← Materials Project cross-validation
├── requirements.txt     ← dependencies
├── .env                 ← API keys (not committed to git)
├── .gitignore
├── notebooks/
│   └── demo.ipynb       ← interactive step-by-step walkthrough
└── examples/
    ├── silicon_diamond.json
    ├── copper_fcc.json
    ├── barium_titanate.json
    ├── gallium_nitride_wurtzite.json
    └── iron_bcc.json
```

---

## ⚙️ Installation

### 1. Clone the repository

```bash
git clone https://github.com/mminotaki/material-identifier.git
cd material-identifier
```

### 2. Create and activate a virtual environment

```bash
python3 -m venv mat_ident_env
source mat_ident_env/bin/activate   # Mac/Linux
mat_ident_env\Scripts\activate      # Windows
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Set your API keys

Get a free Gemini API key at https://aistudio.google.com/apikey

Get a free Materials Project API key at https://materialsproject.org

Create a `.env` file in the project root:

```
GEMINI_API_KEY=your-gemini-key-here
MP_API_KEY=your-materials-project-key-here
```

> ⚠️ Never commit your `.env` file. It is already listed in `.gitignore`.

---

## 🖥️ Usage

**Run all 5 built-in examples:**
```bash
python3 identifier.py --run-examples
```

**Identify a custom material:**
```bash
python3 identifier.py --description "rocksalt magnesium oxide"
```

**Interactive mode:**
```bash
python3 identifier.py
```

**Import in your own code:**
```python
from identifier import identify_material

material, validation = identify_material(
    "rocksalt magnesium oxide",
    output_path="examples/mgo.json"
)
print(material.to_json())
```

---

## 📄 Output Format

Each run produces a structured JSON file:

```json
{
  "formula": "GaN",
  "name": "Gallium Nitride",
  "crystal_system": "hexagonal",
  "space_group_symbol": "P6_3mc",
  "space_group_number": 186,
  "point_group": "6mm",
  "a": 3.189, "b": 3.189, "c": 5.185,
  "alpha": 90.0, "beta": 90.0, "gamma": 120.0,
  "atomic_positions": [
    {"element": "Ga", "x": 0.3333, "y": 0.6667, "z": 0.0, "wyckoff_position": "2b"},
    {"element": "N",  "x": 0.3333, "y": 0.6667, "z": 0.375, "wyckoff_position": "2b"}
  ],
  "source": "LLM-inferred",
  "confidence": "high",
  "notes": null,
  "validation": {
    "status": "validated",
    "mp_id": "mp-804",
    "parameter_comparison": {
      "a": {"gemini": 3.189, "mp": 3.189, "diff": 0.0},
      "c": {"gemini": 5.185, "mp": 5.192, "diff": 0.007}
    }
  },
  "electronic_properties": {
    "band_gap_ev": 1.56,
    "is_gap_direct": true,
    "is_metal": false,
    "gw_recommended": true,
    "bse_recommended": true,
    "note": "Semiconductor/insulator — GW/BSE applicable"
  }
}
```


---

## 📊 Example Runs

| Description | Formula | Space Group | Validation | GW/BSE |
|---|---|---|---|---|
| "silicon in the diamond cubic structure" | Si | Fd-3m #227 | mismatch (conventional vs primitive cell) | ✅ |
| "face-centred cubic copper" | Cu | Fm-3m #225 | mismatch (conventional vs primitive cell) | ❌ metal |
| "a perovskite oxide with titanium and barium" | BaTiO3 | P4mm #99 | ✅ validated | ✅ |
| "wurtzite gallium nitride" | GaN | P6_3mc #186 | ✅ validated | ✅ |
| "iron in the body-centred cubic structure" | Fe | Im-3m #229 | mismatch (magnetic phase) | ❌ metal |

Full output files are available in the `examples/` folder.

---

## 🔭 Scope and Limitations

**Supported materials:**
- Elemental metals and semiconductors (Si, Cu, Fe, Al, Au...)
- Binary compounds (GaN, GaAs, NaCl, MgO...)
- Ternary oxides and perovskites (BaTiO3, SrTiO3...)
- Common structure types: FCC, BCC, diamond cubic, wurtzite, rocksalt, perovskite

**Not supported:**
- Highly complex or recently synthesised materials with limited literature coverage
- Disordered, amorphous, or partially ordered materials
- Materials requiring precise experimental lattice parameters

**Known limitations:**
- Lattice parameter mismatches may reflect conventional vs primitive cell differences rather than errors
- Input validation uses the same LLM and may occasionally pass non-material descriptions
- Free tier API is limited to 20 requests/day


---

## 📦 Dependencies

| Package | Purpose |
|---|---|
| `google-genai` | Gemini API client |
| `python-dotenv` | Loads API keys from `.env` file |
| `mp-api` | Materials Project database client |
| `pymatgen` | Crystal structure objects and analysis |

---

## ⚖️ License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.