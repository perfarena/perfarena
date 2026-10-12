from collections import defaultdict

def redistribute_characters(words):
    n = len(words)
    char_count = defaultdict(int)
    
    for word in words:
        for char in word:
            char_count[char] += 1
    
    char_occurrences = char_count.values()
    
    if sum(char_occurrences) % n != 0:
        return False
    else:
        return True

# Test the solution
words = ["hello", "world"]
result = redistribute_characters(words)
print(result)