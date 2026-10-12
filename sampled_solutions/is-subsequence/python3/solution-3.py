def isSubsequence(s, t):
    p1 = 0
    p2 = 0
    
    while p1 < len(s) and p2 < len(t):
        if s[p1] == t[p2]:
            p1 += 1
        p2 += 1
    
    return p1 == len(s)

# Test the function
s = "abc"
t = "ahbgdc"
print(isSubsequence(s, t))  # Output: True