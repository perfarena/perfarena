# Create a counter for the magazine characters
mag = Counter(magazine)

# Iterate through the characters of the ransom note
for c in ransomNote:
    if mag[c] == 0:
        return False
    else:
        mag[c] -= 1

return True