# Initialize an empty map to store alphabet occurrences
alphabet_map = {}

# Traverse through the sentence and update the map
for char in sentence:
    if char.isalpha():
        alphabet_map[char] = alphabet_map.get(char, 0) + 1

# Check if all alphabets from 'a' to 'z' are present
if len(alphabet_map) == 26:
    return True
else:
    return False