def is_pangram(sentence):
    letters = [0] * 26  # Initialize an array to store counts of each letter
    for char in sentence:
        if char.isalpha():  # Check if the character is a letter
            index = ord(char) - ord('a')  # Map the character to an index
            letters[index] += 1  # Increment the count for the letter
    return all(count > 0 for count in letters)  # Check if all counts are greater than zero

# Test the function
sentence = "the quick brown fox jumped over the lazy dog"
print(is_pangram(sentence))  # Output: True