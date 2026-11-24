def first_unique_char_ascii(s):
    freq = [0] * 26
    
    for c in s:
        freq[ord(c) - ord('a')] += 1
    
    for i, c in enumerate(s):
        if freq[ord(c) - ord('a')] == 1:
            return i
    
    return -1

# Test the function
input_str = "leetcode"
result = first_unique_char_ascii(input_str)
print(result)  # Output: 0