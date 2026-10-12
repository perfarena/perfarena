def isSubsequence(s, t):
    if len(s) > len(t):
        return False
    
    i, j = 0, 0
    while j < len(t):
        if s[i] == t[j]:
            i += 1
        if i == len(s):
            return True
        j += 1
    
    return False

# Example usage
S = "ABC"
T = "AHBGDC"
print(isSubsequence(S, T))  # Output: True