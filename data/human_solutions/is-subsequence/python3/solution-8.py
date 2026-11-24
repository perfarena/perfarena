def isSubsequence(s, t):
    pointer_s = 0
    pointer_t = 0
    
    while pointer_s < len(s) and pointer_t < len(t):
        if s[pointer_s] == t[pointer_t]:
            pointer_s += 1
        pointer_t += 1
    
    return pointer_s == len(s)

# Example usage
s1 = "abc"
t1 = "ahbgdc"
print(isSubsequence(s1, t1))  # Output: True

s2 = "axc"
t2 = "ahbgdc"
print(isSubsequence(s2, t2))  # Output: False