def is_pangram(sentence):
    s = sentence
    for c in range(ord('a'), ord('z')+1):
        if s.count(chr(c)) == 0:
            return False
    return True

# Test the function
sentence = "the quick brown fox jumps over the lazy dog"
result = is_pangram(sentence)
print(result)  # Output should be True