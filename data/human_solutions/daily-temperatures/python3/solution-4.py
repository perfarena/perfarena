def dailyTemperatures(t):
    n = len(t)
    result = [0] * n
    stack = []

    for i in range(n):
        while stack and t[i] > t[stack[-1]]:
            prev_index = stack.pop()
            result[prev_index] = i - prev_index

        stack.append(i)

    return result