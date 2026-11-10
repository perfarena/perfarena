def can_construct_ransom_note(ransom_note, magazine):
    count = [0] * 26
    
    for char in magazine:
        count[ord(char) - ord('a')] += 1
    
    for char in ransom_note:
        idx = ord(char) - ord('a')
        count[idx] -= 1
        if count[idx] < 0:
            return False
    
    return True

# Test the function
ransom_note = "aac"
magazine = "aabbcc"
result = can_construct_ransom_note(ransom_note, magazine)
print(result)  # Output: True