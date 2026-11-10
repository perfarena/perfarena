# Initialize variables
word_count = 0
new_sentence = ""

# Iterate through each character in the sentence
for char in sentence:
    if char == " ":
        word_count += 1
        new_sentence += " "
    else:
        new_sentence += char.lower()

# Display the new sentence
print(new_sentence)