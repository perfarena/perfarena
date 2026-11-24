# Initialize variables
target_text = "[Music] [Music] [Music] [Applause] [Music] so [Music] you"
extracted_words = ""

# Iterate through the characters in the target text
for char in target_text:
    if char == "[" or char == "]":
        extracted_words += char

# Remove square brackets and spaces
extracted_words = extracted_words.replace("[", "").replace("]", "").replace(" ", "")

print(extracted_words)