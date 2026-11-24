def can_construct_ransom_note(magazine, ransom_note):
    char_frequency = {}
    
    # Create a frequency count for characters in the magazine
    for char in magazine:
        if char in char_frequency:
            char_frequency[char] += 1
        else:
            char_frequency[char] = 1
    
    # Check if ransom note can be formed
    for char in ransom_note:
        if char in char_frequency and char_frequency[char] > 0:
            char_frequency[char] -= 1
        else:
            return False
    
    return True

# Test cases
print(can_construct_ransom_note("magazine", "ransom"))  # Output: False
print(can_construct_ransom_note("aaabbcc", "abc"))       # Output: True
print(can_construct_ransom_note("hello", "world"))       # Output: False