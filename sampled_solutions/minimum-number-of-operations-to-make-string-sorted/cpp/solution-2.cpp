#include <unordered_map>
#include <string>

bool isGoodString(std::string s) {
    std::unordered_map<char, int> charMap;
    
    // Count character occurrences
    for (char c : s) {
        charMap[c]++;
    }
    
    // Find the frequency of one character
    int frequency = charMap.begin()->second;
    
    // Check if all characters have the same frequency
    for (auto& x : charMap) {
        if (x.second != frequency) {
            return false;
        }
    }
    
    return true;
}

int main() {
    std::string s = "your_input_string_here";
    if (isGoodString(s)) {
        std::cout << "The string is good." << std::endl;
    } else {
        std::cout << "The string is not good." << std::endl;
    }
    
    return 0;
}