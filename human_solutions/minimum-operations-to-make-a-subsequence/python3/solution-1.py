def is_panagram(sentence):
    alphabet = 'abcdefghijklmnopqrstuvwxyz'
    found_letters = [False] * 26  # Initialize a boolean array to track letter presence

    for char in sentence:
        if char.isalpha():  # Check if the character is a letter
            found_letters[ord(char) - ord('a')] = True  # Mark the letter as found

    return all(found_letters)  # Return true if all letters are found, false otherwise

# Test the function
sentence = "the quick brown fox jumps over the lazy dog"
print(is_panagram(sentence))  # Output: True