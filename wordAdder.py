from sort import main

input_file = "wordstoadd.txt"
output_file = "wordlist.txt"

with open(input_file, "r") as f1:
    words_to_add = f1.read().splitlines()
with open(output_file, "r") as f2:
    words = f2.read().splitlines()
with open(output_file, "w") as f2:
    f2.write("\n".join(words+words_to_add))


sort_input_file = "wordlist.txt"
criteria = ["unique", "long", "ralpha"]

main(sort_input_file, output_file, criteria)