import unittest

from tacman.detect import detect_machine, parse_os_release

NO_PM = lambda name: None


def machine(text, which=NO_PM):
    return detect_machine(os_release_text=text, which=which, is_root=False)


class DetectTests(unittest.TestCase):
    def test_parse_os_release_handles_quotes_and_comments(self):
        info = parse_os_release('# comment\nID=ubuntu\nNAME="Ubuntu"\nID_LIKE=\'debian\'\n\nbroken line\n')
        self.assertEqual(info, {"ID": "ubuntu", "NAME": "Ubuntu", "ID_LIKE": "debian"})

    def test_debian_and_ubuntu(self):
        for distro in ("debian", "ubuntu"):
            m = machine("ID=%s\nNAME=X" % distro)
            self.assertEqual((m.family, m.pkg_manager, m.supported), ("debian", "apt", True))

    def test_mint_via_id_like(self):
        m = machine('ID=somemint\nID_LIKE="ubuntu debian"')
        self.assertEqual((m.family, m.pkg_manager), ("debian", "apt"))

    def test_arch_family(self):
        self.assertEqual(machine("ID=arch").pkg_manager, "pacman")
        self.assertEqual(machine("ID=manjaro\nID_LIKE=arch").family, "arch")
        self.assertEqual(machine("ID=endeavouros\nID_LIKE=arch").family, "arch")
        self.assertEqual(machine("ID=newarchspin\nID_LIKE=arch").family, "arch")

    def test_unknown_distro_is_unsupported(self):
        m = machine("ID=fedora\nID_LIKE=rhel")
        self.assertEqual((m.family, m.pkg_manager, m.supported), (None, None, False))

    def test_unknown_distro_falls_back_to_package_manager_binary(self):
        m = machine("ID=weird", which=lambda n: "/usr/bin/pacman" if n == "pacman" else None)
        self.assertEqual((m.family, m.pkg_manager), ("arch", "pacman"))

    def test_missing_os_release(self):
        m = machine("")
        self.assertEqual((m.distro_id, m.distro_name, m.supported), ("unknown", "Unknown Linux", False))

    def test_root_counts_as_sudo_ready(self):
        m = detect_machine(os_release_text="ID=arch", which=NO_PM, is_root=True)
        self.assertTrue(m.to_dict()["sudo_cached"])


if __name__ == "__main__":
    unittest.main()
