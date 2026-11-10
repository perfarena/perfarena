def reverse_string(s):
    left = 0
    right = len(s) - 1
    
    while left < right:
        # Swap characters at left and right pointers
        temp = s[left]
        s[left] = s[right]
        s[right] = temp
        
        # Move pointers
        left += 1
        right -= 1
    
    return s

# Test the function
input_str = "hello"
output_str = reverse_string(list(input_str))
print("Reversed String:", "".join(output_str))