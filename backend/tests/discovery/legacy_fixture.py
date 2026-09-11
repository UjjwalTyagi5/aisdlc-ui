"""A small legacy repository, written to disk, for the Discovery analysis tests.

Deliberately shaped like the thing Track 3 exists for — a .NET Framework 4.5.2
WebForms application with a class library it references, next to an SDK-style
library that is already on modern .NET, a Node.js 14 service and a Java 8 module —
so every analysis step has something real to find rather than a synthetic one-liner.
"""
from __future__ import annotations

from pathlib import Path

LEGACY_WEB_CSPROJ = """<?xml version="1.0" encoding="utf-8"?>
<Project ToolsVersion="15.0" DefaultTargets="Build" xmlns="http://schemas.microsoft.com/developer/msbuild/2003">
  <PropertyGroup>
    <OutputType>Library</OutputType>
    <RootNamespace>Billing.Web</RootNamespace>
    <TargetFrameworkVersion>v4.5.2</TargetFrameworkVersion>
  </PropertyGroup>
  <ItemGroup>
    <Reference Include="System" />
    <Reference Include="System.Web" />
    <Reference Include="System.ServiceModel" />
    <Reference Include="Newtonsoft.Json, Version=9.0.0.0">
      <HintPath>..\\packages\\Newtonsoft.Json.9.0.1\\lib\\net45\\Newtonsoft.Json.dll</HintPath>
    </Reference>
  </ItemGroup>
  <ItemGroup>
    <Compile Include="Default.aspx.cs" />
    <Content Include="Default.aspx" />
  </ItemGroup>
  <ItemGroup>
    <ProjectReference Include="..\\Billing.Core\\Billing.Core.csproj">
      <Project>{11111111-2222-3333-4444-555555555555}</Project>
      <Name>Billing.Core</Name>
    </ProjectReference>
  </ItemGroup>
</Project>
"""

LEGACY_WEB_PACKAGES = """<?xml version="1.0" encoding="utf-8"?>
<packages>
  <package id="Newtonsoft.Json" version="9.0.1" targetFramework="net452" />
  <package id="WindowsAzure.Storage" version="8.1.4" targetFramework="net452" />
  <package id="Microsoft.AspNet.WebApi.Core" version="5.2.3" targetFramework="net452" />
</packages>
"""

CORE_CSPROJ = """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net6.0</TargetFramework>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Dapper" Version="2.0.123" />
    <PackageReference Include="Serilog">
      <Version>2.12.0</Version>
    </PackageReference>
  </ItemGroup>
</Project>
"""

CORE_TESTS_CSPROJ = """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net6.0</TargetFramework>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="xunit" Version="2.4.2" />
  </ItemGroup>
  <ItemGroup>
    <ProjectReference Include="../../src/Billing.Core/Billing.Core.csproj" />
  </ItemGroup>
</Project>
"""

NODE_PACKAGE = """{
  "name": "billing-notifier",
  "version": "1.0.0",
  "engines": { "node": ">=14" },
  "dependencies": { "express": "^4.17.1", "request": "^2.88.0" },
  "devDependencies": { "jest": "^26.0.0" }
}
"""

POM = """<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.example</groupId>
  <artifactId>billing-batch</artifactId>
  <version>1.0</version>
  <properties>
    <maven.compiler.source>1.8</maven.compiler.source>
    <maven.compiler.target>1.8</maven.compiler.target>
  </properties>
  <dependencies>
    <dependency>
      <groupId>org.springframework</groupId>
      <artifactId>spring-core</artifactId>
      <version>4.3.9.RELEASE</version>
    </dependency>
  </dependencies>
</project>
"""


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def build_legacy_repo(root: Path) -> Path:
    """Write the fixture repository under `root` and return it."""
    web = root / "src" / "Billing.Web"
    _write(web / "Billing.Web.csproj", LEGACY_WEB_CSPROJ)
    _write(web / "packages.config", LEGACY_WEB_PACKAGES)
    _write(web / "Default.aspx", '<%@ Page Language="C#" CodeBehind="Default.aspx.cs" %>\n<html></html>\n')
    _write(
        web / "Default.aspx.cs",
        "using System.Web;\nnamespace Billing.Web {\n"
        + "".join(f"  public class Page{i} {{ public int Run() {{ return {i}; }} }}\n" for i in range(400))
        + "}\n",
    )
    _write(web / "Service.svc", '<%@ ServiceHost Service="Billing.Web.Service" %>\n')

    core = root / "src" / "Billing.Core"
    _write(core / "Billing.Core.csproj", CORE_CSPROJ)
    _write(core / "Invoice.cs", "namespace Billing.Core {\n  public record Invoice(int Id);\n}\n")

    tests = root / "tests" / "Billing.Core.Tests"
    _write(tests / "Billing.Core.Tests.csproj", CORE_TESTS_CSPROJ)
    _write(tests / "InvoiceTests.cs", "public class InvoiceTests { }\n")

    node = root / "services" / "notifier"
    _write(node / "package.json", NODE_PACKAGE)
    _write(node / "index.js", "const express = require('express');\nmodule.exports = express;\n")
    _write(node / "index.test.js", "test('x', () => {});\n")

    java = root / "batch"
    _write(java / "pom.xml", POM)
    _write(java / "src" / "main" / "java" / "App.java", "public class App { }\n")

    # Vendored noise that must never be counted as the application's own code.
    _write(root / "src" / "Billing.Web" / "bin" / "junk.cs", "class Junk {}\n")
    _write(root / "services" / "notifier" / "node_modules" / "left-pad" / "index.js", "module.exports=1\n")
    _write(root / "README.md", "# Billing\n")
    return root
