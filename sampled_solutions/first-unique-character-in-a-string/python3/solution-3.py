def firstNonRepeatingCharIndex(s):
    freq = [0] * 26
    
    for char in s:
        freq[ord(char) - ord('a')] += 1
    
    for i in range(len(s)):
        if freq[ord(s[i]) - ord('a')] == 1:
            return i
    
    return -1

# Test the function
s = "bcdaabcb"
result = firstNonRepeatingCharIndex(s)
print("Index of the first non-repeating character:", result)