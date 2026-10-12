def halves_are_alike(s):
    half = len(s) // 2
    vowels = set('aeiou')
    left, right = 0, 0
    
    for i in range(half):
        if s[i].lower() in vowels:
            left += 1
        if s[i + half].lower() in vowels:
            right += 1
    
    return left == right

# Test the function
print(halves_are_alike("book"))  # Output: True
print(halves_are_alike("text"))  # Output: False