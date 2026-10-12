def fizzbuzz_twist(n):
    result = []
    for i in range(1, n + 1):
        if i % 2 == 0 and i % 3 == 0 and i % 5 == 0:
            result.append("frizzbuzz")
        elif i % 2 == 0:
            result.append("frizz")
        elif i % 3 == 0:
            result.append("buzz")
        elif i % 5 == 0:
            result.append("frizz")
        else:
            result.append(str(i))
    return result

# Test the function
n = 15
print(fizzbuzz_twist(n))