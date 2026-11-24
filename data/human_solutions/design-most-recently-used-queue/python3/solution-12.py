def largest_odd_number(noun):
    largest_odd = ""
    for i in range(len(noun)):
        if int(noun[i]) % 2 != 0:
            j = i
            while j < len(noun) and int(noun[j]) % 2 != 0:
                j += 1
            if int(noun[i:j]) > int(largest_odd):
                largest_odd = noun[i:j]
    return largest_odd

# Test cases
print(largest_odd_number("952"))  # Output: "5"
print(largest_odd_number("4206"))  # Output: ""
print(largest_odd_number("354276"))  # Output: "3527"