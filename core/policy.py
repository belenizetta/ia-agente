class ChangePolicy:
    def check(self, changes):
        return {
            "passed": True,
            "reason": "default-allow"
        }
