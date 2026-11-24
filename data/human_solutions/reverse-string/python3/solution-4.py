def reverseString(s):
    left = 0
    right = len(s) - 1
    
    while left < right:
        s[left], s[right] = s[right], s[left]
        left += 1
        right -= 1

# Test the function
input_string = ['h', 'e', 'l', 'l', 'o']
reverseString(input_string)
print(input_string)  # Output: ['o', 'l', 'l', 'e', 'h']