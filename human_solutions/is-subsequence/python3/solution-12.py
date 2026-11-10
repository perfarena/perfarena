def is_subsequence(s, T):
    I, J = 0, 0
    while I < len(s) and J < len(T):
        if s[I] == T[J]:
            I += 1
        J += 1
    return I == len(s)