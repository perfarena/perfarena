def dailyTemperatures(T):
    stack = []
    output = [0] * len(T)

    for i in range(len(T)):
        while stack and T[i] > T[stack[-1]]:
            prev_index = stack.pop()
            output[prev_index] = i - prev_index

        stack.append(i)

    return output