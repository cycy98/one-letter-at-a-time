import regex as re
import sys

def extract_words(text):
    pattern = r"[A-Za-z\-']*"
    return re.findall(pattern, text.lower())

def apply_sorts(words, criteria):
    if "unique" in criteria:
        words = list(set(words))
        criteria = [c for c in criteria if c != "unique"]

    for criterion in reversed(criteria):
        if criterion == "alpha":
            words.sort()
        elif criterion == "ralpha":
            words.sort(reverse=True)
        elif criterion == "short":
            words.sort(key=len)
        elif criterion == "long":
            words.sort(key=len, reverse=True)
        else:
            raise ValueError(f"Unknown sort criterion: {criterion}")

    return words

def main(input_file, output_file, criteria):
    with open(input_file, "r", encoding="utf-8") as f:
        text = f.read()

    words = extract_words(text)
    words = apply_sorts(words, criteria)
    words.remove("")   

    with open(output_file, "w", encoding="utf-8") as f:
        f.write("\n".join(words))

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python sort.py input.txt output.txt [options]")
        sys.exit(1)

    input_file = sys.argv[1]
    output_file = sys.argv[2]

    criteria = sys.argv[3:]
    if not criteria:
        criteria = ["alpha"]

    main(input_file, output_file, criteria)
