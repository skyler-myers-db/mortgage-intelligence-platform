from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INCLUDE = ['README.md', 'CLAUDE.md', 'docs/implementation-plan.md', 'frontend/src/App.tsx', 'backend/main.py']

for rel in INCLUDE:
    p = ROOT / rel
    print(f'\n--- {rel} ---')
    print(p.read_text()[:4000])
