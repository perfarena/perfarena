def is_vowel(char):
    vowels = "aeiouAEIOU"
    return char in vowels

def check_equal_vowels(S):
    L = len(S)
    C1, C2 = 0, 0
    
    for i in range(L // 2):
        if is_vowel(S[i]):
            C1 += 1
    
    for i in range(L // 2, L):
        if is_vowel(S[i]):
            C2 += 1
    
    return C1 == C2

# Example usage
S = "example"
result = check_equal_vowels(S)
print(result)