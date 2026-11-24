def redistribute_characters(words):
    counter = {}
    for word in words:
        for char in word:
            counter[char] = counter.get(char, 0) + 1

    total_items = len(words)
    for count in counter.values():
        if count % total_items != 0:
            return False

    return True

# Test the function
words = ["hey", "how's", "it", "going", "leak", "code", "1897"]
result = redistribute_characters(words)
print(result)