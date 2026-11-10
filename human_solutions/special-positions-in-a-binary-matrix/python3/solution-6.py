def halvesAreAlike(s):
    n = len(s)
    countA = 0
    countB = 0
    
    for i in range(n // 2):
        if s[i].lower() in ['a', 'e', 'i', 'o', 'u']:
            countA += 1
    
    for i in range(n // 2, n):
        if s[i].lower() in ['a', 'e', 'i', 'o', 'u']:
            countB += 1
    
    return countA == countB

# Test cases
print(halvesAreAlike("book"))  # Output: False
print(halvesAreAlike("textbook"))  # Output: False