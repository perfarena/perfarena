def halvesAreAlike(s):
    # Split the string into two halves
    s1 = s[:len(s)//2]
    s2 = s[len(s)//2:]
    
    # Define a string containing all vowels
    vowels = "aeiouAEIOU"
    
    # Initialize counters for vowels in both halves
    counter1 = 0
    counter2 = 0
    
    # Iterate through the characters in both halves
    for char in s1:
        if char in vowels:
            counter1 += 1
    
    for char in s2:
        if char in vowels:
            counter2 += 1
    
    # Return True if counts of vowels in both halves are equal
    return counter1 == counter2

# Test the function with a sample input
input_str = "hello"
print(halvesAreAlike(input_str))  # Output: False