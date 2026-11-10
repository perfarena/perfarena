def length_of_longest_substring(s):
    result = 0
    left = 0
    seen = {}
    
    for right, char in enumerate(s):
        if char in seen and seen[char] >= left:
            left = seen[char] + 1
        seen[char] = right
        result = max(result, right - left + 1)
    
    return result