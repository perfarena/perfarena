#include <iostream>

int main() {
    std::string test1 = "hello";
    std::string test2 = "elite code";
    
    std::cout << reverseVowels(test1) << std::endl;  // Output: "holle"
    std::cout << reverseVowels(test2) << std::endl;  // Output: "olite cede"
    
    return 0;
}