# PDF Annotations Transfer

A tiny CLI tool to transfer text-markup annotations (highlights, underlines, squiggles) and their reply notes from an old PDF to a new/updated PDF by matching annotation text. The script tries exact matches first, then falls back to a fuzzy (Levenshtein) search.

## Features
- Transfers highlights, underlines and squiggly annotations.
- Preserves simple reply notes attached to transferred annotations.
- Uses exact search first and configurable fuzzy matching as a fallback.
- Copies the new PDF's Table of Contents into the output file.

## Motivation
This tool was created because [@ghislainfourny](https://github.com/ghislainfourny) is a very enthusiastic professor who frequently updates his [Big Data textbook](https://ghislainfourny.github.io/big-data-textbook/), and I kept losing personal notes when switching PDF versions — so this script helps preserve those annotations across versions.

## Requirements
- Python 3.7+
- PyMuPDF (fitz)
- python-Levenshtein

You can install dependencies with:

```bash
pip install -r requirements.txt
```

If you have problems with `requirements.txt`, install directly:

```bash
pip install PyMuPDF python-Levenshtein
```

## Usage
Run the script from the command line. Basic usage:

```bash
python main.py <old_pdf> <new_pdf> <output_pdf> [fuzzy_ratio] [base_allowance]
```

Examples:

```powershell
# use defaults (fuzzy_ratio=0.3, base_allowance=5)
python main.py old_version.pdf new_version.pdf new_with_annotations.pdf

# custom fuzzy parameters
python main.py old_version.pdf new_version.pdf new_with_annotations.pdf 0.4 6
```

Notes:
- Page indices are handled internally (0-based). The script applies simple safeguards to avoid transferring annotations to pages that are too far from the original.
- The script prints progress and a summary to the console. Fuzzy-transferred annotations include a note in the annotation content indicating the match was fuzzy.

## License
This repository includes a `LICENSE` file — see that file for license details.

## Troubleshooting
- If a dependency import fails, ensure you installed packages into the same Python environment used to run the script.
- If annotations aren't transferred, check that the annotation text in the old PDF is selectable text (not an image). The script matches text content.

---
Raise an issue or PR if you want stricter matching, GUI, or batch processing.
