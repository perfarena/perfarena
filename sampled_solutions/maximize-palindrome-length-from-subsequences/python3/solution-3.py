from collections import defaultdict

def redistribute_characters(words):
    length = len(words)
    char_dict = defaultdict(int)

    for string in words:
        for char in string:
            char_dict[char] += 1

    for count in char_dict.values():
        if count % length != 0:
            return False

    return True

# Test the function
words = ["a", "c", "a", "a", "b", "c", "BC", "veryy"]
result = redistribute_characters(words)
print(result)