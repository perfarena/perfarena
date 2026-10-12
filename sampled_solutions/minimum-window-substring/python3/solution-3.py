def find_shortest_ABC_substring_length(s):
    minLength = float('inf')
    start = -1
    
    for i in range(len(s)):
        if s[i:i+3] == 'ABC':
            if start != -1:
                length = i - start + 3
                minLength = min(minLength, length)
            start = i
    
    return minLength

# Test the function
s = "200 Hello friends, welcome to the new video of Tech and today we have problem number 22 Edison, today is Banswara day, our minimum is Window 7 Singh, it is okay, so I have made the work easy in this, it shows you that this problem is a high level problem."
print(find_shortest_ABC_substring_length(s))  # Output: 3