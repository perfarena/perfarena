def reverse_string(s):
    low = 0
    high = len(s) - 1
    
    while low < high:
        s[low], s[high] = s[high], s[low]
        low += 1
        high -= 1

# Example usage
string_array = ["h", "e", "l", "l", "o"]
reverse_string(string_array)
print(string_array)  # Output: ["o", "l", "l", "e", "h"]