def check_equal_occurrences(s):
    freq = [0] * 26
    
    for char in s:
        index = ord(char) - ord('a')
        freq[index] += 1
    
    first_freq = 0
    for f in freq:
        if f > 0:
            first_freq = f
            break
    
    for f in freq:
        if f > 0 and f != first_freq:
            return "force"
    
    return "true"

# Test the function
input_str = "abacbc"
print(check_equal_occurrences(input_str))  # Output: true

input_str = "aaabb"
print(check_equal_occurrences(input_str))  # Output: force