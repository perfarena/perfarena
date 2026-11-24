def isSubsequence(s: str, t: str) -> bool:
    sIndex, tIndex = 0, 0
    
    while sIndex < len(s) and tIndex < len(t):
        if s[sIndex] == t[tIndex]:
            sIndex += 1
        tIndex += 1
    
    return sIndex == len(s)

# Test the function
s = "ABC"
t = "ahbgdc"
print(isSubsequence(s, t))  # Output: True