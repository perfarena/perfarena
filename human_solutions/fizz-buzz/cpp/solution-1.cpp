#include <vector>
#include <string>

std::vector<std::string> fizzBuzz(int n) {
    std::vector<std::string> answer(n);
    int i = 1;
    while (i <= n) {
        if (i % 3 == 0 && i % 5 == 0) {
            answer[i - 1] = "fizzbuzz";
        } else if (i % 3 == 0) {
            answer[i - 1] = "fizz";
        } else if (i % 5 == 0) {
            answer[i - 1] = "buzz";
        } else {
            answer[i - 1] = std::to_string(i);
        }
        i++;
    }
    return answer;
}