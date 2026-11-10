# Create a map to store character counts
char_count = [0] * 26

# Iterate through each word and character
for word in words:
    for char in word:
        index = ord(char) - 97
        char_count[index] += 1

# Check if characters can be evenly distributed
for count in char_count:
    if count % len(words) != 0:
        return False

return True