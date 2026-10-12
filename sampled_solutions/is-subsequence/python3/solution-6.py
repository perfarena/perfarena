def isSubsequence(s, T):
    if len(s) == 0:
        return True
    
    sPointer = 0
    TPointer = 0
    
    while TPointer < len(T):
        if s[sPointer] == T[TPointer]:
            sPointer += 1
        if sPointer == len(s):
            return True
        TPointer += 1
    
    return False