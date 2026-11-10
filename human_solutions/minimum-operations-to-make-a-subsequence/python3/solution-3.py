def check_sentence(sentence):
    # Initialize an array to store the frequency of each letter
    freq = [0] * 26
    
    # Traverse through the sentence and update the frequency array
    for char in sentence:
        if char.isalpha():
            index = ord(char.lower()) - ord('a')
            freq[index] += 1
    
    # Check if all letters are present at least once
    for count in freq:
        if count == 0:
            return False
    
    return True

# Test the function
sentence = "The quick brown fox jumps over the lazy dog"
result = check_sentence(sentence)
print(result)  # Output should be True