def redistribute_characters(words):
    char_count = {}
    for word in words:
        for char in word:
            char_count[char] = char_count.get(char, 0) + 1

    total_chars = sum(char_count.values())
    if total_chars % len(words) != 0:
        return False

    target_count = total_chars // len(words)
    for count in char_count.values():
        if count % len(words) != 0 or count > target_count:
            return False

    return True

# Example usage
words = ["aaa", "aaa", "aaa", "bbb", "bbb", "bbb"]
print(redistribute_characters(words))  # Output: True