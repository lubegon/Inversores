import json
from collections import Counter

with open('storage/values-monitors.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

names = [m['name'] for m in data['monitors']]
print(f"Total en JSON: {len(names)}")
print(f"Total únicos: {len(set(names))}")

# Detectar vacíos
vacios = [n for n in names if not n.strip()]
if vacios:
    print("Monitores vacíos:", vacios)

# Detectar duplicados
from collections import Counter
dupes = [name for name, count in Counter(names).items() if count > 1]
if dupes:
    print("Duplicados:", dupes)
else:
    print("No hay duplicados ni vacíos.")
