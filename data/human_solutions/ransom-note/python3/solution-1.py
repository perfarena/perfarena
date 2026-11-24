def canConstruct(note, magazine):
    construct = True
    for char in note:
        if char in magazine:
            magazine = magazine.replace(char, '', 1)
        else:
            construct = False
            break
    return construct

# Example usage
note = "aabb"
magazine = "aabbc"
print(canConstruct(note, magazine))  # Output: True