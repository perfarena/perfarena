# Define two pointers for s and T
s_pointer = 0
t_pointer = 0

# Iterate over both strings
while s_pointer < len(s) and t_pointer < len(T):
    # Check if characters match
    if s[s_pointer] == T[t_pointer]:
        s_pointer += 1
    t_pointer += 1

# Check if s_pointer reached the end of s
return s_pointer == len(s)