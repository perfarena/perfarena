from collections import defaultdict

def redistribute_characters(words):
    char_count = defaultdict(int)
    
    for word in words:
        for char in word:
            char_count[char] += 1
    
    word_count = len(words)
    
    for count in char_count.values():
        if count % word_count != 0:
            return False
    
    return True

# Test the function
words = ["abc", "bca", "cab"]
result = redistribute_characters(words)
print(result)  # Output should be True